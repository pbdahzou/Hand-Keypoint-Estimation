import matplotlib.pyplot as plt
import numpy as np
import torch
import tqdm

plt.switch_backend('agg')
from lib.Mytransforms import denormalize
from lib.visualization import vis_kpt


def PCK(pred, gt, img_side_len, alpha=0.2):
    """
    Calculate the PCK measure
    :param pred: predicted key points, [N, C, 2]
    :param gt: ground truth key points, [N, C, 2]
    :param img_side_len: max(width, height)
    :param alpha: normalized coefficient
    :return: PCK of current batch, number of correctly detected key points of current batch
    """
    norm_dis = alpha * img_side_len
    dis = (pred.double() - gt) ** 2
    # [N, C]
    dis = torch.sum(dis, dim=2) ** 0.5
    nkpt = (dis < norm_dis).float().sum()
    return nkpt.item() / dis.numel(), nkpt.item()


def PCK_curve_pnts(sp, pred, gt, img_side_len):
    nkpts = [PCK(pred, gt, img_side_len, alpha=a)[1] for a in sp]
    return nkpts


def gaussian_kernel(size_w, size_h, center_x, center_y, sigma):
    gridy, gridx = np.mgrid[0:size_h, 0:size_w]
    D2 = (gridx - center_x) ** 2 + (gridy - center_y) ** 2
    return np.exp(-D2 / 2.0 / sigma / sigma)


def get_kpts(maps, img_h=368.0, img_w=368.0):
    # maps (1,15,46,46) for labels
    maps = maps.clone().cpu().data.numpy()
    all_kpts = []
    for heat_map in maps:
        kpts = []
        for m in heat_map:
            h, w = np.unravel_index(m.argmax(), m.shape)
            x = int(w * img_w / m.shape[1])
            y = int(h * img_h / m.shape[0])
            kpts.append([x, y])
        all_kpts.append(kpts)
    return torch.from_numpy(np.array(all_kpts))


def evaluate(base_net, loader, img_size, pred_net_1=None, pred_net_2=None, status=None, vis=False, logger=None,
             disp_interval=50, show_gt=True, is_target=True):
    """
    :param base_net: model to be evaluated
    :param loader: dataloader to be evaluated
    :param img_size: width/height of img_size (width == height)
    :param pred_net_1: network for prediction
    :param pred_net_2: another network for prediction
    :param status: which method is applied
    :param vis: show kpts on images or not
    :param logger: logger for tensorboardX
    :param disp_interval: interval of display
    :param show_gt: show ground truth or not, disabled if vis=False
    :param is_target: is from target domain or not, disabled if vis=False
    :return: PCK@0.05, PCK@0.2
    """
    assert base_net is not None, 'ERROR: base net is NOT specified!'
    assert pred_net_1 is not None or pred_net_2 is not None, 'ERROR: prediction nets are all NOT specified!'

    pred_nets = []
    if pred_net_1 is not None:
        pred_nets.append(pred_net_1)
    if pred_net_2 is not None:
        pred_nets.append(pred_net_2)

    device = next(base_net.parameters()).device

    nets = [base_net] + pred_nets
    previous_states = []
    for net in nets:
        previous_states.append(net.training)
        net.eval()

    thresholds = np.linspace(0, 0.2, 21)

    tot_nkpts = [0] * thresholds.shape[0]
    tot_pnt = 0
    idx = 0
    domain_prefix = 'tgt' if is_target else 'src'

    # dataset-specific statistics
    mean = loader.dataset.mean
    std = loader.dataset.std
    with torch.no_grad():
        for (inputs, *_, gt_kpts) in tqdm.tqdm(
                loader, desc='Eval {}/{}'.format(domain_prefix, status), ncols=80, total=len(loader), leave=False
        ):

            img_side_len = img_size
            inputs = inputs.to(device)

            # get head_maps for one image
            feats = base_net(inputs)
            if len(pred_nets) == 1:
                heats = pred_nets[0](feats)
            else:
                heats = (pred_nets[0](feats) + pred_nets[1](feats)) / 2

            # get predicted key points
            kpts = get_kpts(heats, img_h=img_side_len, img_w=img_side_len)

            tot_pnt += kpts.numel() / 2

            nkpts = PCK_curve_pnts(thresholds, kpts, gt_kpts[..., :2], img_side_len)
            for i in range(len(tot_nkpts)):
                tot_nkpts[i] += nkpts[i]

            if vis and idx % disp_interval == 0:
                # take the first image of the current batch for visualization
                denorm_img = denormalize(inputs[0], mean, std)
                if show_gt:
                    vis_kpt(gt_pnts=gt_kpts[0, ..., :2], img=denorm_img,
                            save_name='{}_{}_gt_kpt/{}'.format(domain_prefix, status, idx // disp_interval), logger=logger)
                vis_kpt(pred_pnts=kpts[0], img=denorm_img,
                        save_name='{}_{}_pred_kpt/{}'.format(domain_prefix, status, idx // disp_interval), logger=logger)
            idx += 1

    # recover the state
    for state, net in zip(previous_states, nets):
        net.train(state)

    for i in range(len(tot_nkpts)):
        tot_nkpts[i] /= tot_pnt

    # draw PCK curve
    plt.ylim(0, 1.)
    plt.grid()
    pck_line, = plt.plot(thresholds, tot_nkpts)

    logger.add_figure('{}_{}_PCK_curve'.format(domain_prefix, status), pck_line.figure)

    return tot_nkpts[5], tot_nkpts[-1]
